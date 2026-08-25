"""`hosted_entrypoint.py` against in-memory fakes — no real postgres, no real network.

`asyncio.run` drives every `async def` seam here, matching `test_hosted_scheduler.py`'s own
convention (no pytest-asyncio dependency in this repo). Verification for this file was done by
importing it and calling each `test_*` function directly, not via a `pytest`
invocation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import stat
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fi.alk.harness import hosted_entrypoint as he
from fi.alk.harness import outbound as ob
from fi.alk.harness.bundle_v2 import (
    BUNDLE_V2_SCHEMA_VERSION,
    EnvironmentBundleV2,
    ManagedEngine,
    compute_inputs_digest,
    seal_bundle_v2,
)
from fi.alk.harness.hosted_scheduler import (
    Call,
    CallAborted,
    CallOutcome,
    HostedScheduler,
    ReceiptFailure,
    ResultReceipt,
    RunResult,
)
from fi.alk.harness.job import (
    AgentConnection,
    ArtifactLevel,
    ExecutionMode,
    HarnessArtifactPolicy,
    HarnessJob,
    HarnessStage,
    RepositorySource,
    SourceKind,
    SourceVisibility,
)
from fi.alk.harness.process_runtime import (
    EnvironmentRuntime,
    ProcessRuntimeError,
    RuntimeEndpoint,
    RuntimeState,
)
from fi.simulate.runtime.spec import RuntimeRequirements, SecretRef

SCHEMA_SQL = b"CREATE TABLE riders (id int);\n"
SEED_SQL = b"INSERT INTO riders VALUES (1);\n"
TARGET_PROVIDER_ALIAS = "LIVEKIT_API_KEY"


# =================================================================================================
# Bundle fixture — mirrors test_process_preflight.py's own helper (not imported: this file is
# self-contained per the "touch only your two new files" rule).
# =================================================================================================


def _base_manifest_body() -> dict[str, Any]:
    return {
        "schema_version": BUNDLE_V2_SCHEMA_VERSION,
        "name": "demo",
        "runtime": {"kind": "process", "control_service": "agent", "evidence_seam": "http_tool"},
        "processes": [
            {
                "name": "postgres", "kind": "managed", "engine": "postgres", "version": "16",
                "user": "svc-data", "depends_on": [],
            },
            {
                "name": "agent", "kind": "source", "working_directory": ".",
                "build_commands": [["pip", "install", "-r", "requirements.txt"]],
                "run_command": ["python", "agent.py"],
                "environment": {
                    "DATABASE_URL": "{{DATABASE_URL}}", "LIVEKIT_AGENT_NAME": "agent-w{{WORLD_INDEX}}",
                },
                "secret_purposes": ["target_provider"], "user": "svc-agent", "depends_on": ["postgres"],
            },
        ],
        "capabilities": {
            "database": {
                "protocol": "postgres", "service": "postgres", "configuration_name": "DATABASE_URL",
            },
        },
        "readiness": [],
        "provenance": {
            "source_kind": "repository", "repository": "org/repo", "source_digest": "c" * 64,
        },
        "metadata": {},
    }


def _write_bundle(root: Path) -> EnvironmentBundleV2:
    root.mkdir(parents=True, exist_ok=True)
    body = _base_manifest_body()
    file_contents = {"db/schema.sql": SCHEMA_SQL, "db/seed.sql": SEED_SQL}
    files: list[dict[str, Any]] = []
    for relative, content in file_contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(
            {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        )
    body["files"] = files
    digest = compute_inputs_digest(
        root, ["db/schema.sql"], ["db/seed.sql"], engine=ManagedEngine.POSTGRES, version="16"
    )
    body["seed"] = {
        "stores": [
            {
                "capability": "database", "migrations": ["db/schema.sql"], "seed_files": ["db/seed.sql"],
                "baseline": {"strategy": "template_database", "inputs_digest": digest},
                "sentinel": {"query": "SELECT count(*) FROM riders", "expected": "1"},
            }
        ]
    }
    body["digest"] = "sha256:" + "0" * 64
    normalized = EnvironmentBundleV2.model_validate(body)
    body["digest"] = seal_bundle_v2(normalized)
    (root / "manifest.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return EnvironmentBundleV2.model_validate(body)


def _job(
    *, connector: str = "vapi", parallelism: int = 1,
    artifacts: HarnessArtifactPolicy | None = None,
) -> HarnessJob:
    return HarnessJob(
        job_id="job-1", run_id="run-1", execution=ExecutionMode.HOSTED,
        source=RepositorySource(
            kind=SourceKind.GITHUB, repository="org/repo", visibility=SourceVisibility.PUBLIC,
            commit_sha="a" * 40,
        ),
        agent=AgentConnection(
            connector=connector,
            secret_refs={
                TARGET_PROVIDER_ALIAS: SecretRef(
                    manager="platform-vault", key="secret-id", purpose="target_provider"
                )
            },
        ),
        scenario_count=2,
        seed=1234,
        runtime=RuntimeRequirements(parallelism=parallelism),
        **({"artifacts": artifacts} if artifacts is not None else {}),
    )


def _write_job(path: Path, job: HarnessJob) -> None:
    path.write_text(job.model_dump_json(), encoding="utf-8")


# =================================================================================================
# Capabilities fixture.
# =================================================================================================


def _capabilities(*, attempt_id: str = "attempt-1") -> ob.HostedCapabilities:
    base = f"https://platform.example/simulate/api/harness/attempts/{attempt_id}"
    return ob.HostedCapabilities.model_validate(
        {
            "schema_version": ob.CAPABILITIES_SCHEMA_VERSION,
            "job_id": "job-1",
            "attempt_id": attempt_id,
            "attempt_number": 1,
            "fence": "fence-1",
            "expires_at": "2999-01-01T00:00:00.000Z",
            "token": "bearer-token",
            "endpoints": {
                "events": f"{base}/events/",
                "results": f"{base}/results/",
                "artifacts": f"{base}/artifacts/",
                "scenarios": f"{base}/scenarios/",
            },
        }
    )


# =================================================================================================
# FakeTransport — a minimal in-memory platform. Routes on a URL substring, not a full router: this
# module only needs the four channel shapes, not a general HTTP mock.
# =================================================================================================


@dataclass
class FakeTransport:
    fence_after: int | None = None  # 1-based call count at which every further call 403s.
    fence_on_url_substring: str | None = None  # once a URL matches, that call and every later one 403s.
    # fences the specific events POST whose batch carries a `type: "terminal"` record --
    # `emit_terminal()`'s own spool append succeeds, so this reproduces the fence landing on the
    # network flush that delivers the terminal event, not before it.
    fence_on_terminal_event: bool = False
    _fenced: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    event_records: list[dict[str, Any]] = field(default_factory=list)
    receipts: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, bytes] = field(default_factory=dict)
    manifests: list[dict[str, Any]] = field(default_factory=list)
    scenarios_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        data: bytes | Any | None = None,
        timeout: float = 30.0,
    ) -> ob.TransportResponse:
        del timeout
        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
        if self.fence_on_url_substring is not None and self.fence_on_url_substring in url:
            self._fenced = True
        if (
            self.fence_on_terminal_event
            and "/events/" in url
            and method == "POST"
            and isinstance(data, (bytes, bytearray))
            and b'"type":"terminal"' in bytes(data)
        ):
            self._fenced = True
        if self._fenced or (self.fence_after is not None and len(self.calls) >= self.fence_after):
            return ob.TransportResponse(
                status_code=403,
                body={"error": "fenced", "message": "attempt superseded", "retryable": False},
                headers={},
            )
        if "/events/" in url and method == "POST":
            body_bytes = data if isinstance(data, (bytes, bytearray)) else b""
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {"events": []}
            events = body.get("events", [])
            self.event_records.extend(events)
            watermark = max((e["sequence"] for e in events), default=0)
            return ob.TransportResponse(200, {"acked_through_sequence": watermark, "rejected": []}, {})
        if "/results/" in url and method == "POST" and json_body is not None:
            key = (json_body["job_id"], json_body["scenario_key"])
            existed = key in self.receipts
            self.receipts[key] = json_body
            return ob.TransportResponse(200 if existed else 201, {}, {})
        if url.endswith("/manifest/") and method == "POST" and json_body is not None:
            self.manifests.append(json_body)
            return ob.TransportResponse(200, {}, {})
        if "/artifacts/" in url and method == "PUT":
            digest = url.rstrip("/").rsplit("/", 1)[-1]
            payload = data if isinstance(data, (bytes, bytearray)) else b"".join(data)
            existed = digest in self.artifacts
            self.artifacts[digest] = bytes(payload)
            return ob.TransportResponse(200 if existed else 201, {}, {})
        if "/scenarios/" in url and method == "POST" and json_body is not None:
            self.scenarios_calls.append((url, json_body))
            if url.endswith("/provision/"):
                ids = {key: f"platform-{key}" for key in json_body.get("scenario_keys", [])}
                return ob.TransportResponse(200, {"result": {"scenario_ids": ids}}, {})
            return ob.TransportResponse(200, {"result": {"ok": True}}, {})
        return ob.TransportResponse(
            404, {"error": "not_found", "message": f"unmapped route: {url}", "retryable": False}, {}
        )

    def terminal_events(self) -> list[dict[str, Any]]:
        return [record for record in self.event_records if record.get("type") == "terminal"]


# =================================================================================================
# Fake provisioner / world / scenario / call runner.
# =================================================================================================


class FakeProvisioner:
    name = "fake-process"  # mirrors `ProcessRuntimeProvider.name` so passthrough is testable.

    def __init__(self, instances: int = 1, *, always_unhealthy: bool = False) -> None:
        self.instances = instances
        self.always_unhealthy = always_unhealthy  # every healthy() probe fails.
        self.provision_calls = 0
        self.reset_calls = 0
        self.healthy_calls = 0
        self.closed = False
        self._busy = False
        self._runtimes = {
            i: EnvironmentRuntime(
                runtime_id=f"digest:w{i}", world_index=i, bundle_digest="digest",
                state=RuntimeState.READY, endpoints={},
            )
            for i in range(instances)
        }

    async def _serialized(self) -> None:
        assert not self._busy, "provider called reentrantly"
        self._busy = True
        try:
            await asyncio.sleep(0)
        finally:
            self._busy = False

    async def provision(
        self, bundle: Any, *, source: Path, bundle_dir: Path, work_directory: Path,
        contract: Any | None = None, instances: int = 1,
    ) -> list[EnvironmentRuntime]:
        del bundle, source, bundle_dir, work_directory, contract
        await self._serialized()
        self.provision_calls += 1
        return [self._runtimes[i] for i in range(instances)]

    async def reset(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> None:
        del work_directory
        await self._serialized()
        self.reset_calls += 1
        runtime.state = RuntimeState.READY

    async def healthy(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> bool:
        del runtime, work_directory
        # v1.12 folds `healthy` into the same non-reentrant set as provision/reset/close --
        # this is the one verb that previously did NOT call `_serialized()`, so a `SerializingProvider`
        # gap here would pass silently without it.
        await self._serialized()
        self.healthy_calls += 1
        return not self.always_unhealthy

    async def close(self, *, work_directory: Path) -> None:
        del work_directory
        await self._serialized()
        self.closed = True


class FakeWorld:
    def __init__(self, world_index: int, rng: Any) -> None:
        self.world_index = world_index
        self.rng = rng

    def state(self, table: str | None = None) -> dict[str, list[dict[str, Any]]]:
        del table
        return {}

    def put(self, collection: str, record: dict[str, Any], *, key: str = "") -> dict[str, Any]:
        del collection, key
        return record

    def change(self, collection: str, key: str, changes: dict[str, Any], *, by: str = "") -> int:
        del collection, key, changes, by
        return 1

    def drop(self, collection: str, key: str = "", *, by: str = "") -> int:
        del collection, key, by
        return 1

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Call:
        raise NotImplementedError

    def query(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        del sql, params
        return []

    def read_only(self) -> "FakeWorld":
        return self


class FakeWorldFactory:
    async def create(self, runtime: EnvironmentRuntime, *, rng: Any) -> FakeWorld:
        return FakeWorld(runtime.world_index, rng)


@dataclass
class FakeSubGoal:
    name: str
    should_hold: bool
    judged: str = "yes"

    def check(self, world: Any, calls: Any) -> object:
        del world, calls
        return None if self.should_hold else "the agent did not do it"


@dataclass
class FakeScenario:
    scenario_key: str
    scenario_id: str
    sub_goals: list[FakeSubGoal]

    def setup(self, world: Any) -> object:
        del world
        return None

    def ready(self, world: Any) -> object:
        del world
        return None


class FakeCallRunner:
    """Uploads a transcript through the adapter BEFORE returning, and can optionally trip the
    cancel signal the instant a named scenario starts (for the cancel-mid-run test)."""

    def __init__(
        self,
        adapter: he.OutboundAdapter,
        *,
        cancel_path: Path | None = None,
        cancel_on_scenario: str | None = None,
        cancel_reason: str = "user_canceled",
        delay_seconds: float = 0.05,
    ) -> None:
        self._adapter = adapter
        self._cancel_path = cancel_path
        self._cancel_on_scenario = cancel_on_scenario
        self._cancel_reason = cancel_reason
        self._delay_seconds = delay_seconds

    async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> CallOutcome:
        del runtime
        if self._cancel_path is not None and scenario.scenario_key == self._cancel_on_scenario:
            self._cancel_path.write_text(
                json.dumps({"reason": self._cancel_reason}), encoding="utf-8"
            )
        await asyncio.sleep(self._delay_seconds)
        transcript = json.dumps(
            [{"speaker_role": "assistant", "content": f"hello from {scenario.scenario_key}"}]
        ).encode("utf-8")
        artifact_id = await self._adapter.upload_artifact(
            transcript, kind=ob.ArtifactKind.TRANSCRIPT, scenario_key=scenario.scenario_key
        )
        now = _rfc3339(datetime.now(timezone.utc))
        return CallOutcome(
            calls=(Call(name="tool", arguments={}, result="ok", ok=True, error="", refused=False, at=0.0),),
            turns=1, started_at=now, ended_at=now, duration_ms=10,
            transcript_artifact=artifact_id, recording_artifacts=(),
        )


def _rfc3339(value: datetime) -> str:
    return ob.format_rfc3339_millis(value)


class FakeScenarioSource:
    def __init__(self, scenarios: list[FakeScenario]) -> None:
        self._scenarios = scenarios

    async def build(
        self, job: HarnessJob, bundle: Any, scenarios_client: he.ScenariosClient, *, pool: Any,
        world_factory: Any,
    ) -> list[FakeScenario]:
        del job, bundle, pool, world_factory
        await asyncio.to_thread(
            scenarios_client.provision,
            {"scenario_keys": [s.scenario_key for s in self._scenarios]},
        )
        await asyncio.to_thread(scenarios_client.begin, {"scenario_ids": {}})
        return self._scenarios


# =================================================================================================
# Deps builder.
# =================================================================================================


@dataclass
class Harness:
    tmp: Path
    work: Path
    source: Path
    output: Path
    job_path: Path
    transport: FakeTransport
    provisioner: FakeProvisioner
    deps: he.HostedEntrypointDeps


def _build_harness(
    *,
    scenarios: list[FakeScenario],
    fence_after: int | None = None,
    fence_on_url_substring: str | None = None,
    fence_on_terminal_event: bool = False,
    cancel_on_scenario: str | None = None,
    cancel_reason: str = "user_canceled",
    corrupt_bundle: Callable[[Path], None] | None = None,
    instances: int = 1,
    always_unhealthy: bool = False,
    parallelism: int = 1,
    build_output: dict[str, Any] | None = None,
    artifacts: HarnessArtifactPolicy | None = None,
) -> Harness:
    tmp = Path(tempfile.mkdtemp(prefix="p10-e2e-"))
    work = tmp / "work"
    source = work / "source"
    output = work / "artifacts"
    bundle_dir = work / he.DEFAULT_BUNDLE_DIR_NAME
    source.mkdir(parents=True, exist_ok=True)
    _write_bundle(bundle_dir)
    if corrupt_bundle is not None:
        corrupt_bundle(bundle_dir)

    job_path = tmp / "job.json"
    _write_job(job_path, _job(parallelism=parallelism, artifacts=artifacts))

    if build_output is not None:
        # `write_build_output` (process_runtime.py) writes here; no test previously did, so
        # the baseline_frozen/parallelism_degraded block ran only its
        # empty-dict fallback in every prior test.
        output.mkdir(parents=True, exist_ok=True)
        (output / "build.json").write_text(json.dumps(build_output), encoding="utf-8")

    capabilities = _capabilities()
    transport = FakeTransport(
        fence_after=fence_after, fence_on_url_substring=fence_on_url_substring,
        fence_on_terminal_event=fence_on_terminal_event,
    )
    provisioner = FakeProvisioner(instances=instances, always_unhealthy=always_unhealthy)

    cancel_path = tmp / "cancel.json"

    holder: dict[str, he.OutboundAdapter] = {}

    def build_call_runner(adapter: he.OutboundAdapter) -> FakeCallRunner:
        holder["adapter"] = adapter
        return FakeCallRunner(
            adapter, cancel_path=cancel_path, cancel_on_scenario=cancel_on_scenario,
            cancel_reason=cancel_reason,
        )

    deps = he.HostedEntrypointDeps(
        load_capabilities=lambda: capabilities,
        bundle_source=he.DefaultBundleSource(),
        scenario_source=FakeScenarioSource(scenarios),
        build_transport=lambda: transport,
        build_provider=lambda: provisioner,
        build_call_runner=build_call_runner,
        build_world_factory=lambda work_directory: FakeWorldFactory(),
        cancel_path=cancel_path,
        secrets_path=tmp / "secrets.json",
        install_sigterm_handler=lambda cancel_state: (lambda: None),
        flush_window_seconds=5.0,
    )
    return Harness(
        tmp=tmp, work=work, source=source, output=output, job_path=job_path, transport=transport,
        provisioner=provisioner, deps=deps,
    )


def _run(harness: Harness) -> int:
    return asyncio.run(he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps))


def _build_adapter(
    transport: FakeTransport, *, extra_secret_values: tuple[str, ...] = ()
) -> he.OutboundAdapter:
    """Adapter-only fixture for tests that drive `OutboundAdapter` directly (no `run_job`) --
    mirrors `test_redaction_end_to_end_secret_never_crosses_any_channel`'s own inline construction,
    factored out for reuse across other adapter-level tests."""
    capabilities = _capabilities()
    channel_state = ob.ChannelState()
    retry_policy = ob.RetryPolicy()
    tmp = Path(tempfile.mkdtemp(prefix="p10-adapter-"))
    events_spool = ob.OutboundSpool(tmp / "spool", "events", sequenced=True)
    events_client = ob.EventsClient(
        capabilities, events_spool, transport, retry_policy=retry_policy, channel_state=channel_state,
    )
    results_client = ob.ResultsClient(
        capabilities, transport, retry_policy=retry_policy, channel_state=channel_state,
    )
    artifacts_client = ob.ArtifactsClient(
        capabilities, transport, retry_policy=retry_policy, channel_state=channel_state,
    )
    return he.OutboundAdapter(
        capabilities,
        events_spool=events_spool,
        events_client=events_client,
        results_client=results_client,
        artifacts_client=artifacts_client,
        channel_state=channel_state,
        extra_secret_values=extra_secret_values,
    )


# =================================================================================================
# Pure-logic unit tests.
# =================================================================================================


def test_resolve_parallelism_reads_the_raw_value_without_clamping() -> None:
    # `RuntimeRequirements.parallelism` now exists -- `resolve_parallelism`
    # must return it RAW; clamping here would make `parallelism_out_of_range` unreachable.
    assert he.resolve_parallelism(_job(parallelism=1)) == 1
    assert he.resolve_parallelism(_job(parallelism=8)) == 8
    # An out-of-range value is preflight's to reject (§2e.7), not this function's to launder --
    # `RuntimeRequirements.parallelism` itself only enforces `ge=1`, so a too-large W passes model
    # validation and must still reach `resolve_parallelism` unclamped.
    assert he.resolve_parallelism(_job(parallelism=99)) == 99


def test_out_of_range_parallelism_is_rejected_by_preflight_not_clamped() -> None:
    # Confirms an out-of-range W reaches a
    # `parallelism_out_of_range` preflight rejection (§2e.7), never a silently clamped W=8 run.
    async def scenario() -> None:
        harness = _build_harness(scenarios=[], parallelism=20)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        assert harness.provisioner.provision_calls == 0  # rejected before any provision, like §2e.
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        payload = terminals[0]["payload"]
        assert payload["failure"]["code"] == "parallelism_out_of_range"
        assert payload["failure"]["domain"] == "environment"
        assert payload["failure"]["stage"] == "validating_environment"

    asyncio.run(scenario())


def test_job_secret_purposes_maps_alias_to_purpose() -> None:
    job = _job()
    assert he.job_secret_purposes(job) == {TARGET_PROVIDER_ALIAS: "target_provider"}


def test_peek_secret_values_reads_without_deleting(tmp_path_factory: Path | None = None) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="p10-secrets-"))
    path = tmp / "secrets.json"
    path.write_text(json.dumps({"LIVEKIT_API_KEY": "sk-super-secret"}), encoding="utf-8")
    values = he.peek_secret_values(path)
    assert values == ("sk-super-secret",)
    assert path.exists()  # non-destructive read -- the provisioner still owns load-and-delete.


def test_peek_secret_values_missing_file_is_empty() -> None:
    assert he.peek_secret_values(Path("/nonexistent/does-not-exist.json")) == ()


def test_row_counts_for_capability_returns_the_matching_store() -> None:
    build_output = {"stores": [{"capability": "database", "row_counts": {"riders": 3}}]}
    assert he.row_counts_for_capability(build_output, "database") == {"riders": 3}


def test_row_counts_for_capability_raises_when_the_capability_is_absent() -> None:
    build_output = {"stores": [{"capability": "other", "row_counts": {}}]}
    try:
        he.row_counts_for_capability(build_output, "database")
    except he.WorldFactoryError:
        pass
    else:
        raise AssertionError("expected WorldFactoryError")


def test_cancel_state_reads_reason_from_file() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="p10-cancel-"))
    path = tmp / "cancel.json"
    state = he.CancelState(path)
    assert state.requested() is False
    path.write_text(json.dumps({"reason": "ttl_exceeded"}), encoding="utf-8")
    assert state.requested() is True
    assert state.reason() is ob.TerminalReason.TTL_EXCEEDED


def test_serializing_provider_serializes_concurrent_provision_calls() -> None:
    async def scenario() -> None:
        fake = FakeProvisioner(instances=1)
        wrapped = he.SerializingProvider(fake)
        work = Path(tempfile.mkdtemp(prefix="p10-serial-"))
        results = await asyncio.gather(
            wrapped.provision(None, source=work, bundle_dir=work, work_directory=work, instances=1),
            wrapped.provision(None, source=work, bundle_dir=work, work_directory=work, instances=1),
        )
        # The real, load-bearing check is INSIDE `FakeProvisioner._serialized()` (an `assert not
        # self._busy` around a real `await` yield point) -- if `SerializingProvider` let both calls
        # run concurrently, that assertion would raise and this whole coroutine would fail instead
        # of returning cleanly. `provision_calls == 2` only confirms both eventually ran.
        assert fake.provision_calls == 2
        assert all(len(r) == 1 for r in results)

    asyncio.run(scenario())


def test_serializing_provider_serializes_healthy_against_provision() -> None:
    # v1.12 folds `healthy` into the SAME non-reentrant set as provision/reset/close --
    # `FakeProvisioner.healthy` is the one verb that previously did not call `_serialized()`
    # (see its own definition above), so this is the only test that would have caught a
    # `SerializingProvider` that forgot to wrap `healthy()` in its lock.
    async def scenario() -> None:
        fake = FakeProvisioner(instances=1)
        wrapped = he.SerializingProvider(fake)
        work = Path(tempfile.mkdtemp(prefix="p10-serial-healthy-"))
        runtime = fake._runtimes[0]
        await asyncio.gather(
            wrapped.provision(None, source=work, bundle_dir=work, work_directory=work, instances=1),
            wrapped.healthy(runtime, work_directory=work),
        )
        assert fake.provision_calls == 1
        assert fake.healthy_calls == 1

    asyncio.run(scenario())


def test_serializing_provider_name_passes_through() -> None:
    # §4's `RuntimeProvider` Protocol declares `name: str` -- the wrapper must not hide it.
    fake = FakeProvisioner(instances=1)
    wrapped = he.SerializingProvider(fake)
    assert wrapped.name == "fake-process"


def test_scenarios_client_provision_unwraps_the_result_envelope() -> None:
    capabilities = _capabilities()
    transport = FakeTransport()
    client = he.ScenariosClient(capabilities, transport)
    result = client.provision({"scenario_keys": ["a", "b"]})
    assert result == {"scenario_ids": {"a": "platform-a", "b": "platform-b"}}


def test_scenarios_client_fencing_latches_the_shared_channel_state() -> None:
    capabilities = _capabilities()
    transport = FakeTransport(fence_after=1)
    channel_state = ob.ChannelState()
    client = he.ScenariosClient(capabilities, transport, channel_state=channel_state)
    try:
        client.provision({"scenario_keys": []})
    except ob.HostedFencedError:
        pass
    else:
        raise AssertionError("expected HostedFencedError")
    try:
        channel_state.check()
    except ob.HostedFencedError:
        pass
    else:
        raise AssertionError("channel_state should now be latched for every other channel too")


# =================================================================================================
# Targeted orchestration tests.
# =================================================================================================


def test_capabilities_failure_exits_boot_failure_with_no_channel_and_no_event() -> None:
    async def scenario() -> None:
        tmp = Path(tempfile.mkdtemp(prefix="p10-boot-"))
        work = tmp / "work"
        source = work / "source"
        output = work / "artifacts"
        source.mkdir(parents=True, exist_ok=True)
        job_path = tmp / "job.json"
        _write_job(job_path, _job())

        def _raise() -> ob.HostedCapabilities:
            raise ob.CapabilitiesError("capabilities_file_missing", "no file")

        deps = he.HostedEntrypointDeps(load_capabilities=_raise)
        code = await he.run_job(job_path, source, output, deps=deps)
        assert code == he.EXIT_BOOT_FAILURE
        assert code != he.EXIT_FENCED
        assert not (work / he.EVENTS_SPOOL_DIR_NAME).exists()  # no channel was ever built.

    asyncio.run(scenario())


def test_preflight_rejection_reaches_a_failed_terminal_event_before_any_provision() -> None:
    async def scenario() -> None:
        harness = _build_harness(
            scenarios=[],
            corrupt_bundle=lambda bundle_dir: (bundle_dir / "db" / "schema.sql").unlink(),
        )
        code = await he.run_job(
            harness.job_path, harness.source, harness.output, deps=harness.deps
        )
        assert code == he.EXIT_OK
        assert harness.provisioner.provision_calls == 0  # "BEFORE any provision"
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        payload = terminals[0]["payload"]
        assert payload["stage"] == "failed"
        assert payload["failure"]["domain"] == "environment"
        assert payload["failure"]["stage"] == "validating_environment"
        assert payload["failure"]["code"] == "bundle_file_missing"

    asyncio.run(scenario())


def test_hosted_fenced_error_stops_emitting_and_exits_3_with_no_terminal_event() -> None:
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        # Fenced mid-attempt (during the scenario's own receipt push), well after provisioning --
        # proves both halves of the contract: nothing further is ever emitted, AND close() still
        # runs (it is unconditional after scheduler.run(), not gated on fencing).
        harness = _build_harness(scenarios=scenarios, fence_on_url_substring="/results/")
        code = await he.run_job(
            harness.job_path, harness.source, harness.output, deps=harness.deps
        )
        assert code == he.EXIT_FENCED
        assert harness.transport.terminal_events() == []
        assert harness.provisioner.closed is True  # close() still runs on the way out.

    asyncio.run(scenario())


def test_cancel_mid_run_synthesizes_a_skipped_receipt_for_the_unstarted_scenario() -> None:
    async def scenario() -> None:
        order: list[str] = []
        scenarios = [
            FakeScenario("first", "platform-first", [FakeSubGoal("holds", True)]),
            FakeScenario("second", "platform-second", [FakeSubGoal("holds", True)]),
        ]

        class OrderTrackingTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                response = super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )
                if (
                    "/events/" in url and method == "POST"
                    and isinstance(data, (bytes, bytearray))
                    and b'"type":"terminal"' in bytes(data)
                ):
                    order.append("terminal_delivered")
                if (
                    "/results/" in url and method == "POST"
                    and json_body is not None and json_body.get("status") == "skipped"
                ):
                    order.append("skipped_receipt_delivered")
                return response

        harness = _build_harness(scenarios=scenarios, cancel_on_scenario="first", instances=1)
        transport = OrderTrackingTransport()
        harness.deps.build_transport = lambda: transport
        code = await he.run_job(
            harness.job_path, harness.source, harness.output, deps=harness.deps
        )
        assert code == he.EXIT_OK
        terminals = transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["stage"] == "canceled"
        assert terminals[0]["payload"]["reason"] == "user_canceled"
        statuses = {key[1]: body["status"] for key, body in transport.receipts.items()}
        assert statuses.get("first") == "passed"
        assert statuses.get("second") == "skipped"

        # outbound-channels.md v1.3 Sequencing ("terminal event -> skipped receipts ->
        # manifest") -- the skipped receipt for "second" must reach the platform, and strictly
        # after the terminal event, never before or instead of it.
        assert order == ["terminal_delivered", "skipped_receipt_delivered"]

        # the "skipped" receipt body (exact) per outbound-channels.md's own six-field list --
        # not just its `status`.
        skipped_body = transport.receipts[("job-1", "second")]
        assert skipped_body["scenario_attempt"] == 1
        assert skipped_body["world_index"] is None
        assert skipped_body["sub_goals"] == []
        assert skipped_body["evaluations"] == []
        assert skipped_body["call"] is None
        assert skipped_body["failure"] is None

        # a CANCELED run is cut short -- the manifest must say so.
        assert transport.manifests[-1]["complete"] is False

    asyncio.run(scenario())


def test_fenced_run_result_emits_zero_skipped_receipts() -> None:
    # `RunResult.fenced` must gate the entrypoint's own call to
    # `emit_skipped_receipts` -- checked here at the call site itself, not left to the scheduler's
    # own internal no-op. `HostedScheduler.run` is monkeypatched to hand back a fenced `RunResult`
    # regardless of how the real run went, since the real `OutboundAdapter` has no path that lets
    # a channel fence reach `WorldPool.fenced` (it swallows `HostedFencedError` internally --
    # see `OutboundAdapter._guarded`), so this is the only reliable way to exercise the branch
    # end-to-end through `run_job`.
    async def scenario() -> None:
        scenarios = [
            FakeScenario("first", "platform-first", [FakeSubGoal("holds", True)]),
            FakeScenario("second", "platform-second", [FakeSubGoal("holds", True)]),
        ]
        harness = _build_harness(scenarios=scenarios, instances=1)

        fence_exc = ob.HostedFencedError(
            ob.ChannelError(ob.ChannelOutcome.FENCED, None, "fence_mismatch", "attempt superseded")
        )
        original_run = HostedScheduler.run
        original_emit = HostedScheduler.emit_skipped_receipts
        emit_calls: list[RunResult] = []

        async def fenced_run(self: HostedScheduler, scns: Any) -> RunResult:
            real = await original_run(self, scns)
            return RunResult(receipts=real.receipts, aborted=real.aborted, fenced=fence_exc)

        async def counting_emit(self: HostedScheduler, result: RunResult) -> None:
            emit_calls.append(result)
            await original_emit(self, result)

        HostedScheduler.run = fenced_run  # type: ignore[method-assign]
        HostedScheduler.emit_skipped_receipts = counting_emit  # type: ignore[method-assign]
        try:
            code = await he.run_job(
                harness.job_path, harness.source, harness.output, deps=harness.deps
            )
        finally:
            HostedScheduler.run = original_run
            HostedScheduler.emit_skipped_receipts = original_emit

        assert code == he.EXIT_OK
        assert emit_calls == []

    asyncio.run(scenario())


def test_emit_skipped_receipts_failure_does_not_lose_the_terminal_or_the_exit_code() -> None:
    # a failure inside the post-terminal `scheduler.emit_skipped_receipts` call must be
    # swallowed locally (matching every other best-effort post-terminal emission in this module),
    # never mask the terminal already delivered or flip the exit code.
    async def scenario() -> None:
        scenarios = [
            FakeScenario("first", "platform-first", [FakeSubGoal("holds", True)]),
            FakeScenario("second", "platform-second", [FakeSubGoal("holds", True)]),
        ]
        harness = _build_harness(scenarios=scenarios, cancel_on_scenario="first", instances=1)

        original_emit = HostedScheduler.emit_skipped_receipts

        async def poisoned_emit(self: HostedScheduler, result: RunResult) -> None:
            raise RuntimeError("synthetic emit_skipped_receipts failure")

        HostedScheduler.emit_skipped_receipts = poisoned_emit  # type: ignore[method-assign]
        try:
            code = await he.run_job(
                harness.job_path, harness.source, harness.output, deps=harness.deps
            )
        finally:
            HostedScheduler.emit_skipped_receipts = original_emit

        assert code == he.EXIT_OK
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["stage"] == "canceled"
        assert terminals[0]["payload"]["reason"] == "user_canceled"
        statuses = {key[1]: body["status"] for key, body in harness.transport.receipts.items()}
        assert statuses.get("first") == "passed"

    asyncio.run(scenario())


def test_cancel_mid_run_with_ttl_exceeded_reports_that_reason() -> None:
    # the exit-code table's CANCELED branch is only ever exercised with `user_canceled`
    # elsewhere in this file -- `ttl_exceeded` is the contract's other legal reason
    # (`ob.TerminalReason`) and goes through the exact same `CancelState.reason()` path.
    async def scenario() -> None:
        scenarios = [
            FakeScenario("first", "platform-first", [FakeSubGoal("holds", True)]),
            FakeScenario("second", "platform-second", [FakeSubGoal("holds", True)]),
        ]
        harness = _build_harness(
            scenarios=scenarios, cancel_on_scenario="first", cancel_reason="ttl_exceeded",
            instances=1,
        )
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["stage"] == "canceled"
        assert terminals[0]["payload"]["reason"] == "ttl_exceeded"

    asyncio.run(scenario())


def test_malformed_job_json_exits_crashed_with_no_terminal_event() -> None:
    # EXIT_CRASHED -- a malformed job.json exits non-zero with no channel of its own
    # kind (capabilities load already succeeded, but nothing typed can describe the failure).
    async def scenario() -> None:
        tmp = Path(tempfile.mkdtemp(prefix="p10-badjob-"))
        work = tmp / "work"
        source = work / "source"
        output = work / "artifacts"
        source.mkdir(parents=True, exist_ok=True)
        job_path = tmp / "job.json"
        job_path.write_text("{this is not valid json", encoding="utf-8")

        capabilities = _capabilities()
        transport = FakeTransport()
        deps = he.HostedEntrypointDeps(
            load_capabilities=lambda: capabilities,
            build_transport=lambda: transport,
            secrets_path=tmp / "secrets.json",
            install_sigterm_handler=lambda cancel_state: (lambda: None),
        )
        code = await he.run_job(job_path, source, output, deps=deps)
        assert code == he.EXIT_CRASHED
        assert code != he.EXIT_FENCED
        assert transport.terminal_events() == []

    asyncio.run(scenario())


def test_world_pool_exhaustion_reaches_a_failed_terminal_world_pool_exhausted() -> None:
    # `RunResult.aborted` -> a terminal FAILED with domain `infrastructure`, stage
    # `running`, code `world_pool_exhausted`. Driven for real: the single world never passes its
    # `healthy()` probe, so `WorldPool.lease()` exhausts its reconcile budget and raises
    # `NoWorldsAvailable`, which `HostedScheduler.run()` turns into `RunResult.aborted`.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1, always_unhealthy=True)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        payload = terminals[0]["payload"]
        assert payload["stage"] == "failed"
        assert payload["failure"]["domain"] == "infrastructure"
        assert payload["failure"]["stage"] == "running"
        assert payload["failure"]["code"] == "world_pool_exhausted"
        assert harness.provisioner.closed is True

    asyncio.run(scenario())


def test_fence_landing_on_the_final_drain_still_exits_fenced() -> None:
    # a fence that 403s specifically the events
    # POST carrying the terminal event (not an earlier one) must still exit 3 with no terminal
    # event DELIVERED, never a stale pre-drain fence check that misses it.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1, fence_on_terminal_event=True)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_FENCED
        assert harness.transport.terminal_events() == []
        assert harness.provisioner.closed is True

    asyncio.run(scenario())


def test_fence_from_scenarios_client_exits_fenced_not_crashed() -> None:
    # `ScenariosClient._post` re-raises `HostedFencedError` after latching --
    # this must reach `run_job`'s typed handler around `scenario_source.build()`, not fall through
    # to a bare `except Exception` (exit 1, and the world pool leaked).
    async def scenario() -> None:
        harness = _build_harness(scenarios=[], instances=1, fence_on_url_substring="/scenarios/")
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_FENCED
        assert harness.transport.terminal_events() == []
        assert harness.provisioner.closed is True  # the pool must not leak on this path either.

    asyncio.run(scenario())


def test_scenarios_channel_uses_bearer_auth_never_api_key() -> None:
    # outbound-channels.md calls out bearer + X-Harness-Fence by name for pre-allocation --
    # never `X-Api-Key`.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        scenarios_calls = [call for call in harness.transport.calls if "/scenarios/" in call["url"]]
        assert scenarios_calls, "expected at least one call against endpoints.scenarios"
        for call in scenarios_calls:
            assert call["headers"].get("Authorization", "").startswith("Bearer ")
            assert "X-Api-Key" not in call["headers"]
            assert "X-Harness-Fence" in call["headers"]

    asyncio.run(scenario())


def test_process_world_factory_raises_when_no_postgres_endpoint() -> None:
    # `ProcessWorldFactory`/`_find_postgres_endpoint` had zero coverage -- only the pure
    # `row_counts_for_capability` helper was unit-tested.
    async def scenario() -> None:
        tmp = Path(tempfile.mkdtemp(prefix="p10-wf-endpoint-"))
        factory = he.ProcessWorldFactory(tmp)
        runtime = EnvironmentRuntime(
            runtime_id="digest:w0", world_index=0, bundle_digest="digest",
            state=RuntimeState.READY, endpoints={},
        )
        try:
            await factory.create(runtime, rng=random.Random(0))
        except he.WorldFactoryError:
            pass
        else:
            raise AssertionError("expected WorldFactoryError for a runtime with no postgres endpoint")

    asyncio.run(scenario())


def test_process_world_factory_raises_when_build_json_has_no_matching_store() -> None:
    async def scenario() -> None:
        tmp = Path(tempfile.mkdtemp(prefix="p10-wf-store-"))
        (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
        (tmp / "artifacts" / "build.json").write_text(
            json.dumps({"stores": [{"capability": "other", "row_counts": {}}]}), encoding="utf-8"
        )
        factory = he.ProcessWorldFactory(tmp)
        endpoint = RuntimeEndpoint(
            capability="database", protocol="postgres", address="postgresql://u:p@localhost/db",
        )
        runtime = EnvironmentRuntime(
            runtime_id="digest:w0", world_index=0, bundle_digest="digest",
            state=RuntimeState.READY, endpoints={"database": endpoint},
        )
        try:
            await factory.create(runtime, rng=random.Random(0))
        except he.WorldFactoryError:
            pass
        else:
            raise AssertionError("expected WorldFactoryError for a build.json with no matching store")

    asyncio.run(scenario())


def test_build_json_two_stores_emit_two_baseline_frozen_events() -> None:
    # Each store in build.json's stores list gets its own baseline_frozen event, not just the first.
    async def scenario() -> None:
        build_output = {
            "stores": [
                {
                    "capability": "database", "baseline_reference": "ref-database",
                    "inputs_digest": "digest-database",
                },
                {
                    "capability": "cache", "baseline_reference": "ref-cache",
                    "inputs_digest": "digest-cache",
                },
            ],
        }
        harness = _build_harness(scenarios=[], instances=1, build_output=build_output)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        baseline_events = [
            record for record in harness.transport.event_records
            if record.get("type") == "baseline_frozen"
        ]
        assert len(baseline_events) == 2
        refs = {event["payload"]["baseline_ref"] for event in baseline_events}
        assert refs == {"ref-database", "ref-cache"}

    asyncio.run(scenario())


def test_build_json_degrade_payload_matches_the_recorded_values() -> None:
    # `parallelism_degraded`'s payload must mirror build.json's own requested/effective/reason values exactly.
    async def scenario() -> None:
        build_output = {
            "requested_parallelism": 2, "effective_parallelism": 1,
            "degrade_reason": "conformance_gate_failed",
        }
        harness = _build_harness(scenarios=[], instances=1, build_output=build_output)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        degrade_events = [
            record for record in harness.transport.event_records
            if record.get("type") == "parallelism_degraded"
        ]
        assert len(degrade_events) == 1
        payload = degrade_events[0]["payload"]
        assert payload == {"requested": 2, "effective": 1, "reason": "conformance_gate_failed"}

    asyncio.run(scenario())


def test_build_json_fixed_port_at_w1_does_not_crash() -> None:
    # `requested == effective == 1` with
    # `degrade_reason: fixed_port` is not representable as a `parallelism_degraded` event
    # (`1 <= effective < requested` fails); this must degrade to a `log`, never crash the run.
    async def scenario() -> None:
        build_output = {
            "requested_parallelism": 1, "effective_parallelism": 1, "degrade_reason": "fixed_port",
        }
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1, build_output=build_output)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        assert harness.provisioner.closed is True  # the pool must not be orphaned by a crash.
        degrade_events = [
            record for record in harness.transport.event_records
            if record.get("type") == "parallelism_degraded"
        ]
        assert degrade_events == []  # not representable -- a log event carries it instead.
        log_events = [
            record for record in harness.transport.event_records if record.get("type") == "log"
        ]
        assert any("fixed_port" in record["payload"]["message"] for record in log_events)
        # a substring shared with the pydantic error text the blanket `except Exception`
        # would ALSO produce if the `effective < requested` guard were reverted -- this is the one
        # phrase that only the guard's own `else` branch ever writes, so it is what actually tells
        # the guard apart from the catch-all swallowing a ValidationError.
        assert any(
            "no parallelism_degraded event is representable" in record["payload"]["message"]
            for record in log_events
        )
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["stage"] == "completed"

    asyncio.run(scenario())


def test_e2e_two_scenarios_one_pass_one_fail_reaches_completed_and_exits_0() -> None:
    async def scenario() -> None:
        scenarios = [
            FakeScenario("passing", "platform-passing", [FakeSubGoal("holds", True)]),
            FakeScenario("failing", "platform-failing", [FakeSubGoal("holds", False)]),
        ]
        harness = _build_harness(scenarios=scenarios, instances=1)
        code = await he.run_job(
            harness.job_path, harness.source, harness.output, deps=harness.deps
        )
        assert code == he.EXIT_OK

        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        payload = terminals[0]["payload"]
        assert payload["stage"] == "completed"
        assert payload["reason"] is None
        assert payload["failure"] is None
        assert payload["scenario_counts"] == {"passed": 1, "failed": 1, "errored": 0, "skipped": 0}

        statuses = {key[1]: body["status"] for key, body in harness.transport.receipts.items()}
        assert statuses == {"passing": "passed", "failing": "failed"}

        # both receipts reference an already-uploaded, already-acked transcript artifact.
        for key, body in harness.transport.receipts.items():
            del key
            transcript = body["call"]["transcript_artifact"]
            assert transcript is not None
            assert transcript.split(":", 1)[1] in harness.transport.artifacts

        assert len(harness.transport.manifests) >= 1
        assert harness.transport.manifests[-1]["complete"] is True
        assert len(harness.transport.manifests[-1]["entries"]) == 2

        assert harness.provisioner.provision_calls >= 1
        assert harness.provisioner.closed is True

        # Scenario pre-allocation (item 3) actually ran against endpoints.scenarios.
        assert any(url.endswith("/provision/") for url, _ in harness.transport.scenarios_calls)
        assert any(url.endswith("/begin/") for url, _ in harness.transport.scenarios_calls)

    asyncio.run(scenario())


# =================================================================================================
# Additional tests closing mutation-honesty gaps -- cases where mutating the guarded code paths
# would have passed the suite unnoticed.
# =================================================================================================


def test_pool_close_backstop_runs_even_when_scenario_source_raises_untyped() -> None:
    # deleting the top-level `finally: pool.close()` backstop passed 29/29 -- every prior
    # test's exception path had an explicit close ahead of it. `MemoryError` matches none of
    # `run_job`'s typed handlers around `scenario_source.build()`, so it propagates straight past
    # the `finally` with no explicit close anywhere on this path -- only the backstop can close it.
    async def scenario() -> None:
        class ExplodingScenarioSource:
            async def build(self, job, bundle, scenarios_client, *, pool, world_factory):
                del job, bundle, scenarios_client, pool, world_factory
                raise MemoryError("boom")

        harness = _build_harness(scenarios=[], instances=1)
        harness.deps.scenario_source = ExplodingScenarioSource()
        raised = False
        try:
            await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        except MemoryError:
            raised = True
        assert raised, "expected the untyped exception to propagate past run_job"
        assert harness.provisioner.closed is True  # only the finally backstop could have done this

    asyncio.run(scenario())


def test_process_runtime_error_maps_to_the_closed_2f_domain_table() -> None:
    # deleting the whole `except ProcessRuntimeError` clause passed 29/29 because
    # `ProcessRuntimeError` never appeared anywhere in the suite -- the §2f domain map
    # was unexecuted. Drives four real codes through `pool.start()` and checks each domain.
    async def run_case(code: str, process: str | None, expected_domain: str) -> None:
        class RaisingProvisioner(FakeProvisioner):
            async def provision(
                self, bundle: Any, *, source: Path, bundle_dir: Path, work_directory: Path,
                contract: Any | None = None, instances: int = 1,
            ) -> list[EnvironmentRuntime]:
                del bundle, source, bundle_dir, work_directory, contract, instances
                raise ProcessRuntimeError("build", code, "synthetic failure", process=process)

        harness = _build_harness(scenarios=[], instances=1)
        harness.deps.build_provider = lambda: RaisingProvisioner(instances=1)
        result = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert result == he.EXIT_OK
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        failure = terminals[0]["payload"]["failure"]
        assert failure["domain"] == expected_domain
        assert failure["stage"] == "building_environment"

    asyncio.run(run_case("build_failed", None, "agent"))
    asyncio.run(run_case("seed_failed", None, "environment"))
    asyncio.run(run_case("spawn_failed", "postgres", "infrastructure"))  # managed process
    asyncio.run(run_case("spawn_failed", "agent", "agent"))  # source process


def test_finish_emits_the_terminal_before_closing_the_pool() -> None:
    # moving `_bounded_close()` before `emit_terminal()` inside `_finish` passed 29/29 --
    # nothing recorded the RELATIVE order of the two. This records both against one shared timeline.
    async def scenario() -> None:
        order: list[str] = []
        tmp = Path(tempfile.mkdtemp(prefix="p10-order-"))
        work = tmp / "work"
        source = work / "source"
        output = work / "artifacts"
        bundle_dir = work / he.DEFAULT_BUNDLE_DIR_NAME
        source.mkdir(parents=True, exist_ok=True)
        _write_bundle(bundle_dir)
        job_path = tmp / "job.json"
        _write_job(job_path, _job(parallelism=1))
        capabilities = _capabilities()

        class OrderTrackingTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                response = super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )
                if (
                    "/events/" in url and method == "POST"
                    and isinstance(data, (bytes, bytearray))
                    and b'"type":"terminal"' in bytes(data)
                ):
                    order.append("terminal_delivered")
                return response

        class OrderTrackingProvisioner(FakeProvisioner):
            async def close(self, *, work_directory: Path) -> None:
                await super().close(work_directory=work_directory)
                order.append("pool_closed")

        transport = OrderTrackingTransport()
        provisioner = OrderTrackingProvisioner(instances=1)
        deps = he.HostedEntrypointDeps(
            load_capabilities=lambda: capabilities,
            bundle_source=he.DefaultBundleSource(),
            scenario_source=FakeScenarioSource([]),
            build_transport=lambda: transport,
            build_provider=lambda: provisioner,
            build_world_factory=lambda work_directory: FakeWorldFactory(),
            cancel_path=tmp / "cancel.json",
            secrets_path=tmp / "secrets.json",
            install_sigterm_handler=lambda cancel_state: (lambda: None),
            flush_window_seconds=5.0,
        )
        code = await he.run_job(job_path, source, output, deps=deps)
        assert code == he.EXIT_OK
        assert order == ["terminal_delivered", "pool_closed"]

    asyncio.run(scenario())


def test_redaction_end_to_end_secret_never_crosses_any_channel() -> None:
    # dropping the `extra_secret_values` threading (event_builder AND build_result_receipt),
    # or the two pre-existing `redact_outbound_text` calls (world_unhealthy.cause,
    # receipt.failure.message), all passed 29/29 -- nothing pinned redaction on the wire. Drives the
    # secret through a log message, world_unhealthy, a terminal failure, and a receipt failure --
    # the free-text fields the contract names -- and reads what actually reached the transport.
    async def scenario() -> None:
        secret = "sk-live-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        capabilities = _capabilities()
        transport = FakeTransport()
        channel_state = ob.ChannelState()
        retry_policy = ob.RetryPolicy()
        tmp = Path(tempfile.mkdtemp(prefix="p10-redact-"))
        events_spool = ob.OutboundSpool(tmp / "spool", "events", sequenced=True)
        events_client = ob.EventsClient(
            capabilities, events_spool, transport, retry_policy=retry_policy,
            channel_state=channel_state,
        )
        results_client = ob.ResultsClient(
            capabilities, transport, retry_policy=retry_policy, channel_state=channel_state,
        )
        artifacts_client = ob.ArtifactsClient(
            capabilities, transport, retry_policy=retry_policy, channel_state=channel_state,
        )
        adapter = he.OutboundAdapter(
            capabilities,
            events_spool=events_spool,
            events_client=events_client,
            results_client=results_client,
            artifacts_client=artifacts_client,
            channel_state=channel_state,
            extra_secret_values=(secret,),
        )

        await adapter.log(level="error", message=f"boom: {secret}")
        await adapter.world_unhealthy(world_index=0, cause=f"probe failed: {secret}")
        await adapter.receipt(
            ResultReceipt(
                scenario_key="s1", scenario_id="platform-s1", scenario_attempt=1, world_index=0,
                status="errored", sub_goals=(), evaluations=(), call=None,
                failure=ReceiptFailure(
                    domain="agent", stage="running", code="call_failed",
                    message=f"failed calling out with {secret}",
                ),
            )
        )
        await adapter.emit_terminal(
            stage=HarnessStage.FAILED,
            failure={
                "domain": "infrastructure", "stage": "building_environment",
                "code": "provision_failed", "message": f"connection failed: {secret}",
            },
        )
        await adapter.drain(complete=True)

        for record in transport.event_records:
            assert secret not in json.dumps(record)
        for body in transport.receipts.values():
            assert secret not in json.dumps(body)

        log_events = [r for r in transport.event_records if r.get("type") == "log"]
        assert any("***" in r["payload"]["message"] for r in log_events)
        world_unhealthy_events = [
            r for r in transport.event_records if r.get("type") == "world_unhealthy"
        ]
        assert any("***" in r["payload"]["cause"] for r in world_unhealthy_events)
        terminal = transport.terminal_events()[0]
        assert "***" in terminal["payload"]["failure"]["message"]
        receipt_body = transport.receipts[("job-1", "s1")]
        assert "***" in receipt_body["failure"]["message"]

    asyncio.run(scenario())


def test_drain_loops_past_a_backlog_larger_than_one_batch_and_still_delivers_the_terminal() -> None:
    # A backlog bigger
    # than one `EVENTS_MAX_BATCH` (100) previously stranded the terminal event, the highest
    # sequence, while still exiting 0. A call runner that logs 260 chatter events before returning
    # reproduces the same shape end to end.
    async def scenario() -> None:
        class ChattyCallRunner:
            def __init__(self, adapter: he.OutboundAdapter, *, log_count: int) -> None:
                self._adapter = adapter
                self._log_count = log_count

            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> CallOutcome:
                del runtime
                for i in range(self._log_count):
                    await self._adapter.log(level="info", message=f"chatter {i}")
                now = _rfc3339(datetime.now(timezone.utc))
                return CallOutcome(
                    calls=(
                        Call(
                            name="tool", arguments={}, result="ok", ok=True, error="",
                            refused=False, at=0.0,
                        ),
                    ),
                    turns=1, started_at=now, ended_at=now, duration_ms=10,
                    transcript_artifact=None, recording_artifacts=(),
                )

        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        harness.deps.build_call_runner = lambda adapter: ChattyCallRunner(adapter, log_count=260)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK

        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1  # must not be stranded behind the backlog

        chatter_logs = [
            record for record in harness.transport.event_records
            if record.get("type") == "log" and "chatter" in record["payload"].get("message", "")
        ]
        assert len(chatter_logs) == 260  # the WHOLE backlog drained, not just the first batch

    asyncio.run(scenario())


def test_call_aborted_with_no_ended_at_still_produces_a_receipt() -> None:
    # `CallAborted.partial.ended_at` is legitimately `None` (the
    # call started but never finished). Before the fix, `build_result_receipt` raised inside
    # `OutboundAdapter.receipt()` (outbound.CallSummary.ended_at is a required str), swallowed by
    # `HostedScheduler._emit`'s blanket `except Exception` -- the scenario reached the wire with NO
    # receipt at all despite `terminal.scenario_counts` claiming one `errored`.
    async def scenario() -> None:
        class AbortingCallRunner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> CallOutcome:
                del runtime
                now = _rfc3339(datetime.now(timezone.utc))
                raise CallAborted(
                    "ran out of time before the call finished",
                    partial=CallOutcome(
                        calls=(), turns=1, started_at=now, ended_at=None, duration_ms=10,
                        transcript_artifact=None, recording_artifacts=(),
                    ),
                )

        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        harness.deps.build_call_runner = lambda adapter: AbortingCallRunner()
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK

        statuses = {key[1]: body["status"] for key, body in harness.transport.receipts.items()}
        assert statuses.get("s1") == "errored"
        receipt_body = harness.transport.receipts[("job-1", "s1")]
        assert receipt_body["call"] is not None
        assert receipt_body["call"]["ended_at"] == receipt_body["call"]["started_at"]

        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["scenario_counts"]["errored"] == 1

    asyncio.run(scenario())


# =================================================================================================
# Additional tests: artifact-level admission coverage, terminal-delivery/receipt-rejection/
# message-capping edge cases, and mutation-survivor gaps around cancellation and the CANCELED manifest.
# =================================================================================================


def test_metadata_only_artifact_level_refuses_transcript_upload_end_to_end() -> None:
    # `_ARTIFACT_LEVEL_FORBIDDEN_KINDS` had zero suite coverage -- emptying the table passed
    # every test. Drives a real `metadata-only` job through the default transcript-uploading call
    # runner and reads what actually reached the transport.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(
            scenarios=scenarios, instances=1,
            artifacts=HarnessArtifactPolicy(level=ArtifactLevel.METADATA_ONLY),
        )
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        assert harness.transport.artifacts == {}  # zero bytes reached the transport

        receipt_body = harness.transport.receipts[("job-1", "s1")]
        assert receipt_body["status"] == "passed"  # a refused upload must not error the scenario
        assert receipt_body["call"]["transcript_artifact"] is None

        log_events = [r for r in harness.transport.event_records if r.get("type") == "log"]
        assert any(
            r["payload"]["level"] == "error"
            and "kind=transcript" in r["payload"]["message"]
            and "forbidden at level=metadata-only" in r["payload"]["message"]
            for r in log_events
        )

    asyncio.run(scenario())


def test_traces_artifact_level_refuses_recording_upload_end_to_end() -> None:
    # A second shape at a different level -- `traces` allows transcripts but forbids
    # recordings. A custom call runner uploads both so the table's per-kind behaviour is visible,
    # not just its per-level all-or-nothing behaviour.
    async def scenario() -> None:
        class RecordingCallRunner:
            def __init__(self, adapter: he.OutboundAdapter) -> None:
                self._adapter = adapter

            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> CallOutcome:
                del runtime
                transcript_id = await self._adapter.upload_artifact(
                    b"transcript-bytes", kind=ob.ArtifactKind.TRANSCRIPT,
                    scenario_key=scenario.scenario_key,
                )
                recording_id = await self._adapter.upload_artifact(
                    b"recording-bytes", kind=ob.ArtifactKind.RECORDING_COMBINED,
                    scenario_key=scenario.scenario_key,
                )
                now = _rfc3339(datetime.now(timezone.utc))
                return CallOutcome(
                    calls=(
                        Call(
                            name="tool", arguments={}, result="ok", ok=True, error="",
                            refused=False, at=0.0,
                        ),
                    ),
                    turns=1, started_at=now, ended_at=now, duration_ms=10,
                    transcript_artifact=transcript_id,
                    recording_artifacts=(recording_id,) if recording_id else (),
                )

        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(
            scenarios=scenarios, instances=1,
            artifacts=HarnessArtifactPolicy(level=ArtifactLevel.TRACES),
        )
        harness.deps.build_call_runner = lambda adapter: RecordingCallRunner(adapter)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK

        receipt_body = harness.transport.receipts[("job-1", "s1")]
        assert receipt_body["status"] == "passed"
        assert receipt_body["call"]["transcript_artifact"] is not None  # traces allows transcripts
        assert receipt_body["call"]["recording_artifacts"] == []  # recordings refused at traces

        transcript_digest = receipt_body["call"]["transcript_artifact"].split(":", 1)[1]
        assert set(harness.transport.artifacts) == {transcript_digest}  # the recording never uploaded

        log_events = [r for r in harness.transport.event_records if r.get("type") == "log"]
        assert any(
            r["payload"]["level"] == "error"
            and "kind=recording_combined" in r["payload"]["message"]
            and "forbidden at level=traces" in r["payload"]["message"]
            for r in log_events
        )

    asyncio.run(scenario())


def test_secrets_unlink_failure_after_the_terminal_does_not_lose_the_terminal_event() -> None:
    # A non-writable secrets directory must not cost the terminal event -- the
    # unlink now runs AFTER `emit_terminal` and is wrapped, so an OSError there is logged, not
    # raised past `run_job`.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        guard_dir = Path(tempfile.mkdtemp(prefix="p10-ro-"))
        secrets_path = guard_dir / "secrets.json"
        secrets_path.write_text('{"A": "x"}', encoding="utf-8")
        os.chmod(guard_dir, stat.S_IRUSR | stat.S_IXUSR)  # read-only directory -> unlink raises
        harness.deps.secrets_path = secrets_path
        try:
            code = await he.run_job(
                harness.job_path, harness.source, harness.output, deps=harness.deps
            )
            assert code == he.EXIT_OK
            terminals = harness.transport.terminal_events()
            assert len(terminals) == 1
            assert terminals[0]["payload"]["stage"] == "completed"
        finally:
            os.chmod(guard_dir, stat.S_IRWXU)

    asyncio.run(scenario())


def test_events_channel_dying_on_the_final_drain_exits_terminal_undelivered() -> None:
    # The events channel dies specifically on the flush that carries the terminal
    # (sustained 5xx exhausts the retry budget) -- exit 0 would falsely claim a flush that never
    # happened. `terminal_undelivered` catches this via the spool watermark never reaching the
    # terminal's own sequence.
    async def scenario() -> None:
        class DyingOnTerminalTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                if (
                    method == "POST" and "/events/" in url
                    and isinstance(data, (bytes, bytearray))
                    and b'"type":"terminal"' in bytes(data)
                ):
                    self.calls.append({"method": method, "url": url, "headers": dict(headers)})
                    return ob.TransportResponse(
                        500, {"error": "server_error", "message": "boom", "retryable": True}, {},
                    )
                return super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )

        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        transport = DyingOnTerminalTransport()
        harness.deps.build_transport = lambda: transport
        harness.deps.retry_policy = lambda: ob.RetryPolicy(
            initial_backoff_seconds=0.0, max_backoff_seconds=0.0, max_attempts=3,
        )
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_TERMINAL_UNDELIVERED
        assert transport.terminal_events() == []  # never actually reached the platform

    asyncio.run(scenario())


def test_platform_rejecting_the_terminal_event_exits_terminal_undelivered() -> None:
    # A different shape: the platform permanently rejects the terminal item itself (a per-item rejection
    # inside an otherwise-200 response), distinct from a dead channel.
    async def scenario() -> None:
        class RejectingTerminalTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                if method == "POST" and "/events/" in url and isinstance(data, (bytes, bytearray)):
                    body = json.loads(bytes(data).decode("utf-8"))
                    events = body.get("events", [])
                    terminal = [e for e in events if e.get("type") == "terminal"]
                    if terminal:
                        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
                        keep = [e for e in events if e.get("type") != "terminal"]
                        self.event_records.extend(keep)
                        rejected = [
                            {"sequence": e["sequence"], "code": "payload_invalid", "message": "nope"}
                            for e in terminal
                        ]
                        watermark = max((e["sequence"] for e in events), default=0)
                        return ob.TransportResponse(
                            200, {"acked_through_sequence": watermark, "rejected": rejected}, {},
                        )
                return super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )

        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        transport = RejectingTerminalTransport()
        harness.deps.build_transport = lambda: transport
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_TERMINAL_UNDELIVERED
        assert transport.terminal_events() == []  # rejected, never landed as a delivered record

    asyncio.run(scenario())


def test_receipt_rejection_by_the_platform_is_logged() -> None:
    # `ResultsClient.push()` returns `ReceiptPushResult(error=...)` on a permanent rejection
    # rather than raising -- nothing inspected the return value before this fix, so the contract's
    # "guest logs, no retry" obligation for e.g. 409 receipt_conflict went unmet.
    async def scenario() -> None:
        class RejectingResultsTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                if method == "POST" and "/results/" in url and json_body is not None:
                    self.calls.append({"method": method, "url": url, "headers": dict(headers)})
                    return ob.TransportResponse(
                        409,
                        {"error": "receipt_conflict", "message": "digest mismatch", "retryable": False},
                        {},
                    )
                return super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )

        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        transport = RejectingResultsTransport()
        harness.deps.build_transport = lambda: transport
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK

        log_events = [r for r in transport.event_records if r.get("type") == "log"]
        assert any(
            r["payload"]["level"] == "error"
            and "s1" in r["payload"]["message"]
            and "receipt_conflict" in r["payload"]["message"]
            for r in log_events
        )

    asyncio.run(scenario())


def test_receipt_failure_message_is_capped_like_the_terminal_message() -> None:
    # `receipt().failure.message` was uncapped while `emit_terminal` caps at 4KB -- an
    # oversized receipt failure message is the most plausible route into a 413 (the same silent-loss
    # shape as a rejected receipt). Adapter-level: no `run_job` needed for a pure formatting check.
    async def scenario() -> None:
        transport = FakeTransport()
        adapter = _build_adapter(transport)
        await adapter.receipt(
            ResultReceipt(
                scenario_key="s1", scenario_id="platform-s1", scenario_attempt=1, world_index=0,
                status="errored", sub_goals=(), evaluations=(), call=None,
                failure=ReceiptFailure(
                    domain="agent", stage="running", code="call_failed", message="y" * 20_000,
                ),
            )
        )
        body = transport.receipts[("job-1", "s1")]
        message = body["failure"]["message"]
        assert len(message) <= he._TERMINAL_FAILURE_MESSAGE_MAX_CHARS
        assert message.endswith("…[truncated]")

    asyncio.run(scenario())


def test_cancel_before_provision_skips_provisioning_entirely() -> None:
    # The pre-provision cancel checkpoint (`hosted_entrypoint.py:1342` area) had no
    # test driving a cancel signal written BEFORE `run_job` starts -- disabling all three
    # checkpoints still passed the whole suite.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(scenarios=scenarios, instances=1)
        harness.deps.cancel_path.write_text(
            json.dumps({"reason": "ttl_exceeded"}), encoding="utf-8"
        )
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        assert harness.provisioner.provision_calls == 0  # canceled before provisioning ever ran
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["stage"] == "canceled"
        assert terminals[0]["payload"]["reason"] == "ttl_exceeded"

        # a CANCELED run is cut short -- the manifest must say so.
        assert harness.transport.manifests[-1]["complete"] is False

    asyncio.run(scenario())


def test_evaluation_wire_coerces_an_int_score_to_float() -> None:
    # `_evaluation_wire`'s `float(score)` coercion had no direct unit test -- an
    # int score digest-mismatches `build_result_receipt`'s own re-derivation and silently drops the
    # whole receipt.
    class IntScoreEvaluation:
        kind = "metric"
        name = "accuracy"
        score = 1
        reason = "int not float"

    wire = he._evaluation_wire(IntScoreEvaluation())
    assert wire["score"] == 1.0
    assert isinstance(wire["score"], float)


def test_receipt_nulls_an_unacked_transcript_artifact_and_logs_it() -> None:
    # A receipt naming an artifact id this adapter never uploaded (and therefore
    # never acked) must ship `null`/`[]` on the wire, not the un-acked id -- the platform would 422
    # (`artifact_unknown`) the whole receipt otherwise.
    async def scenario() -> None:
        transport = FakeTransport()
        adapter = _build_adapter(transport)

        class UnackedCall:
            started_at = "2026-01-01T00:00:00.000Z"
            ended_at = "2026-01-01T00:00:01.000Z"
            duration_ms = 1000
            turns = 1
            transcript_artifact = "sha256:" + "a" * 64  # never uploaded through this adapter
            recording_artifacts = ("sha256:" + "b" * 64,)

        await adapter.receipt(
            ResultReceipt(
                scenario_key="s1", scenario_id="platform-s1", scenario_attempt=1, world_index=0,
                status="passed", sub_goals=(), evaluations=(), call=UnackedCall(), failure=None,
            )
        )
        body = transport.receipts[("job-1", "s1")]
        assert body["call"]["transcript_artifact"] is None
        assert body["call"]["recording_artifacts"] == []

        log_events = [r for r in transport.event_records if r.get("type") == "log"]
        assert any(
            r["payload"]["level"] == "error" and "un-acked transcript" in r["payload"]["message"]
            for r in log_events
        )
        assert any(
            r["payload"]["level"] == "error" and "un-acked recording" in r["payload"]["message"]
            for r in log_events
        )

    asyncio.run(scenario())


def test_pre_run_provision_failure_emits_the_terminal_before_closing_the_pool() -> None:
    # `test_finish_emits_the_terminal_before_...`
    # only drives the COMPLETED path -- a pre-run failure (`_fail` -> `_finish`) is a structurally
    # different entry into `_finish` and needs its own ordering check.
    async def scenario() -> None:
        order: list[str] = []

        class OrderTrackingTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                response = super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )
                if (
                    "/events/" in url and method == "POST"
                    and isinstance(data, (bytes, bytearray))
                    and b'"type":"terminal"' in bytes(data)
                ):
                    order.append("terminal_delivered")
                return response

        class FailingProvisioner(FakeProvisioner):
            async def provision(
                self, bundle: Any, *, source: Path, bundle_dir: Path, work_directory: Path,
                contract: Any | None = None, instances: int = 1,
            ) -> list[EnvironmentRuntime]:
                del bundle, source, bundle_dir, work_directory, contract, instances
                raise ProcessRuntimeError("build", "build_failed", "synthetic", process="agent")

            async def close(self, *, work_directory: Path) -> None:
                await super().close(work_directory=work_directory)
                order.append("pool_closed")

        harness = _build_harness(scenarios=[], instances=1)
        transport = OrderTrackingTransport()
        harness.deps.build_transport = lambda: transport
        harness.deps.build_provider = lambda: FailingProvisioner(instances=1)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        assert order[:2] == ["terminal_delivered", "pool_closed"]
        terminals = transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["failure"]["domain"] == "agent"

    asyncio.run(scenario())


# =================================================================================================
# Additional tests -- the wire retargeted the fence-on-final-drain test to the pre-drain
# check; flush_terminal's and drain()'s bounded loops now cover for each other; the
# post-terminal wire block had no deadline.
# =================================================================================================


def test_fence_on_the_manifest_push_still_exits_fenced_with_the_terminal_already_delivered() -> None:
    # `flush_terminal` now delivers the terminal-carrying POST ahead of `drain()`, so a fence
    # on that POST is caught by the PRE-drain `is_fenced` check in `_finish`, not the post-drain
    # `if fenced: return EXIT_FENCED` line -- `test_fence_landing_on_the_final_
    # drain_still_exits_fenced` no longer exercises that check for the reason its own comment
    # claims. A fence that only starts on `/manifest/` lands strictly inside `drain()` itself
    # (drain()'s own `push_manifest` call), after a clean `flush_terminal`, so `drain()`'s return
    # value -- and therefore only the post-drain check -- is what decides the exit code here. Also
    # pins the observation that a fenced attempt can still have already delivered its terminal,
    # deliberately rather than by accident.
    async def scenario() -> None:
        scenarios = [FakeScenario("s1", "platform-s1", [FakeSubGoal("holds", True)])]
        harness = _build_harness(
            scenarios=scenarios, instances=1, fence_on_url_substring="/manifest/",
        )
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_FENCED
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1  # delivered before the fence ever landed
        assert terminals[0]["payload"]["stage"] == "completed"
        assert harness.provisioner.closed is True

    asyncio.run(scenario())


def test_drain_alone_delivers_a_pre_run_backlog_larger_than_one_batch() -> None:
    # On a pre-run terminal (`_fail` -> `_finish` with no `scheduler_result`), the wire's
    # `flush_terminal` is never called at all -- `drain()`'s own bounded loop is the ONLY delivery
    # mechanism for whatever accumulated beforehand. `test_drain_loops_past_a_backlog_...` only
    # drives a post-run COMPLETED path, where `flush_terminal` already drains everything first and
    # masks a collapsed `drain()` loop. A `ScenarioSource` that logs a large pre-run backlog before
    # raising a typed pre-allocation failure reproduces the shape end to end, on the one path where
    # collapsing `drain()`'s loop to a single flush cannot be covered for by anything else.
    async def scenario() -> None:
        class ChattyPreRunScenarioSource:
            def __init__(self, *, chatter_count: int) -> None:
                self._chatter_count = chatter_count

            async def build(
                self, job: HarnessJob, bundle: Any, scenarios_client: he.ScenariosClient, *,
                pool: Any, world_factory: Any,
            ) -> list[FakeScenario]:
                del job, bundle, scenarios_client, world_factory
                adapter = pool._outbound  # no adapter seam on ScenarioSource itself
                for i in range(self._chatter_count):
                    await adapter.log(level="info", message=f"pre-run chatter {i}")
                raise he.ScenarioPreallocationError(None)

        harness = _build_harness(scenarios=[], instances=1)
        harness.deps.scenario_source = ChattyPreRunScenarioSource(chatter_count=260)
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK

        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1  # must not be stranded behind the pre-run backlog
        assert terminals[0]["payload"]["stage"] == "failed"

        chatter_logs = [
            record for record in harness.transport.event_records
            if record.get("type") == "log" and "pre-run chatter" in record["payload"].get("message", "")
        ]
        assert len(chatter_logs) == 260  # the whole backlog drained, not just the first batch

    asyncio.run(scenario())


def test_flush_terminal_alone_must_deliver_the_terminal_before_a_skipped_receipt_under_backlog() -> None:
    # The converse of the previous test -- on a cancel-mid-run path, `flush_terminal` is the
    # ONLY thing standing between "terminal not yet on the wire" and `emit_skipped_receipts`
    # pushing a receipt straight to `/results/` (that push is not gated on event delivery at all).
    # A backlog bigger than one batch, queued before the cancel is even noticed, means a collapsed
    # `flush_terminal` loop delivers only the first batch and leaves the terminal pending -- the
    # skipped receipt for "second" then reaches the platform BEFORE the terminal does, even though
    # `drain()`'s own (intact) loop mops up the rest a moment later. Ordering, not delivery count,
    # is what only `flush_terminal`'s loop can guarantee here.
    async def scenario() -> None:
        order: list[str] = []
        scenarios = [
            FakeScenario("first", "platform-first", [FakeSubGoal("holds", True)]),
            FakeScenario("second", "platform-second", [FakeSubGoal("holds", True)]),
        ]

        class OrderTrackingTransport(FakeTransport):
            def request(
                self, method: str, url: str, *, headers: dict[str, str],
                json_body: dict[str, Any] | None = None, data: bytes | Any | None = None,
                timeout: float = 30.0,
            ) -> ob.TransportResponse:
                response = super().request(
                    method, url, headers=headers, json_body=json_body, data=data, timeout=timeout,
                )
                if (
                    "/events/" in url and method == "POST"
                    and isinstance(data, (bytes, bytearray))
                    and b'"type":"terminal"' in bytes(data)
                ):
                    order.append("terminal_delivered")
                if (
                    "/results/" in url and method == "POST"
                    and json_body is not None and json_body.get("status") == "skipped"
                ):
                    order.append("skipped_receipt_delivered")
                return response

        class ChattyCancelingCallRunner:
            def __init__(
                self, adapter: he.OutboundAdapter, *, cancel_path: Path, cancel_on_scenario: str,
                chatter_count: int,
            ) -> None:
                self._adapter = adapter
                self._cancel_path = cancel_path
                self._cancel_on_scenario = cancel_on_scenario
                self._chatter_count = chatter_count

            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> CallOutcome:
                del runtime
                if scenario.scenario_key == self._cancel_on_scenario:
                    for i in range(self._chatter_count):
                        await self._adapter.log(level="info", message=f"chatter {i}")
                    self._cancel_path.write_text(
                        json.dumps({"reason": "user_canceled"}), encoding="utf-8"
                    )
                now = _rfc3339(datetime.now(timezone.utc))
                return CallOutcome(
                    calls=(
                        Call(
                            name="tool", arguments={}, result="ok", ok=True, error="",
                            refused=False, at=0.0,
                        ),
                    ),
                    turns=1, started_at=now, ended_at=now, duration_ms=10,
                    transcript_artifact=None, recording_artifacts=(),
                )

        harness = _build_harness(scenarios=scenarios, cancel_on_scenario="first", instances=1)
        transport = OrderTrackingTransport()
        harness.deps.build_transport = lambda: transport
        harness.deps.build_call_runner = lambda adapter: ChattyCancelingCallRunner(
            adapter, cancel_path=harness.deps.cancel_path, cancel_on_scenario="first",
            chatter_count=260,
        )
        code = await he.run_job(harness.job_path, harness.source, harness.output, deps=harness.deps)
        assert code == he.EXIT_OK
        statuses = {key[1]: body["status"] for key, body in transport.receipts.items()}
        assert statuses.get("first") == "passed"
        assert statuses.get("second") == "skipped"

        assert order == ["terminal_delivered", "skipped_receipt_delivered"]

    asyncio.run(scenario())


def test_post_terminal_wire_block_is_bounded_by_the_remaining_flush_window() -> None:
    # `emit_skipped_receipts`/`receipt()` push with `deadline=None` -- a degraded-but-alive
    # events channel could previously retry every skipped scenario's receipt for the full
    # `RetryPolicy` budget regardless of how much of the flush window was already spent, well past
    # the point the gateway tears the sandbox down. A tiny window plus a slow
    # `emit_skipped_receipts` reproduces the shape without a real multi-second stall dominating the
    # suite: `asyncio.wait_for`'s own cancellation cuts the sleep short well before it would run out.
    async def scenario() -> None:
        scenarios = [
            FakeScenario("first", "platform-first", [FakeSubGoal("holds", True)]),
            FakeScenario("second", "platform-second", [FakeSubGoal("holds", True)]),
        ]
        harness = _build_harness(scenarios=scenarios, cancel_on_scenario="first", instances=1)
        harness.deps.flush_window_seconds = 0.2

        original_emit = HostedScheduler.emit_skipped_receipts

        async def slow_emit(self: HostedScheduler, result: RunResult) -> None:
            await asyncio.sleep(2.0)
            await original_emit(self, result)

        HostedScheduler.emit_skipped_receipts = slow_emit  # type: ignore[method-assign]
        started = time.monotonic()
        try:
            code = await he.run_job(
                harness.job_path, harness.source, harness.output, deps=harness.deps
            )
        finally:
            HostedScheduler.emit_skipped_receipts = original_emit
        elapsed = time.monotonic() - started

        assert elapsed < 1.5, f"post-terminal wire block was not bounded: {elapsed:.2f}s"
        assert code == he.EXIT_OK  # the terminal was already delivered before the window ran out
        terminals = harness.transport.terminal_events()
        assert len(terminals) == 1
        assert terminals[0]["payload"]["stage"] == "canceled"

        statuses = {key[1]: body["status"] for key, body in harness.transport.receipts.items()}
        assert statuses.get("first") == "passed"
        assert "second" not in statuses  # window ran out before the skipped receipt could go

        assert harness.provisioner.closed is True  # the finally backstop still closes the pool

    asyncio.run(scenario())


TESTS = [
    test_resolve_parallelism_reads_the_raw_value_without_clamping,
    test_out_of_range_parallelism_is_rejected_by_preflight_not_clamped,
    test_job_secret_purposes_maps_alias_to_purpose,
    test_peek_secret_values_reads_without_deleting,
    test_peek_secret_values_missing_file_is_empty,
    test_row_counts_for_capability_returns_the_matching_store,
    test_row_counts_for_capability_raises_when_the_capability_is_absent,
    test_cancel_state_reads_reason_from_file,
    test_serializing_provider_serializes_concurrent_provision_calls,
    test_serializing_provider_serializes_healthy_against_provision,
    test_serializing_provider_name_passes_through,
    test_scenarios_client_provision_unwraps_the_result_envelope,
    test_scenarios_client_fencing_latches_the_shared_channel_state,
    test_capabilities_failure_exits_boot_failure_with_no_channel_and_no_event,
    test_preflight_rejection_reaches_a_failed_terminal_event_before_any_provision,
    test_hosted_fenced_error_stops_emitting_and_exits_3_with_no_terminal_event,
    test_cancel_mid_run_synthesizes_a_skipped_receipt_for_the_unstarted_scenario,
    test_fenced_run_result_emits_zero_skipped_receipts,
    test_emit_skipped_receipts_failure_does_not_lose_the_terminal_or_the_exit_code,
    test_cancel_mid_run_with_ttl_exceeded_reports_that_reason,
    test_malformed_job_json_exits_crashed_with_no_terminal_event,
    test_world_pool_exhaustion_reaches_a_failed_terminal_world_pool_exhausted,
    test_fence_landing_on_the_final_drain_still_exits_fenced,
    test_fence_from_scenarios_client_exits_fenced_not_crashed,
    test_scenarios_channel_uses_bearer_auth_never_api_key,
    test_process_world_factory_raises_when_no_postgres_endpoint,
    test_process_world_factory_raises_when_build_json_has_no_matching_store,
    test_build_json_two_stores_emit_two_baseline_frozen_events,
    test_build_json_degrade_payload_matches_the_recorded_values,
    test_build_json_fixed_port_at_w1_does_not_crash,
    test_e2e_two_scenarios_one_pass_one_fail_reaches_completed_and_exits_0,
    test_pool_close_backstop_runs_even_when_scenario_source_raises_untyped,
    test_process_runtime_error_maps_to_the_closed_2f_domain_table,
    test_finish_emits_the_terminal_before_closing_the_pool,
    test_redaction_end_to_end_secret_never_crosses_any_channel,
    test_drain_loops_past_a_backlog_larger_than_one_batch_and_still_delivers_the_terminal,
    test_call_aborted_with_no_ended_at_still_produces_a_receipt,
    test_metadata_only_artifact_level_refuses_transcript_upload_end_to_end,
    test_traces_artifact_level_refuses_recording_upload_end_to_end,
    test_secrets_unlink_failure_after_the_terminal_does_not_lose_the_terminal_event,
    test_events_channel_dying_on_the_final_drain_exits_terminal_undelivered,
    test_platform_rejecting_the_terminal_event_exits_terminal_undelivered,
    test_receipt_rejection_by_the_platform_is_logged,
    test_receipt_failure_message_is_capped_like_the_terminal_message,
    test_cancel_before_provision_skips_provisioning_entirely,
    test_evaluation_wire_coerces_an_int_score_to_float,
    test_receipt_nulls_an_unacked_transcript_artifact_and_logs_it,
    test_pre_run_provision_failure_emits_the_terminal_before_closing_the_pool,
    test_fence_on_the_manifest_push_still_exits_fenced_with_the_terminal_already_delivered,
    test_drain_alone_delivers_a_pre_run_backlog_larger_than_one_batch,
    test_flush_terminal_alone_must_deliver_the_terminal_before_a_skipped_receipt_under_backlog,
    test_post_terminal_wire_block_is_bounded_by_the_remaining_flush_window,
]


if __name__ == "__main__":
    failures = 0
    for test_fn in TESTS:
        started = time.monotonic()
        try:
            test_fn()
        except Exception as exc:  # noqa: BLE001 - a direct-invocation runner, not pytest
            failures += 1
            print(f"FAIL {test_fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test_fn.__name__} ({time.monotonic() - started:.2f}s)")
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    raise SystemExit(1 if failures else 0)
